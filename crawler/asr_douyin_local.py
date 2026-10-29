#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr_douyin_local.py — 对已下载到本地的抖音视频做 ASR 口播转写。

背景：
  extract_media_text.py 的 run_asr 是通过「重新下载直链」再转写，直链会过期，
  对历史库存补跑会全部失败。本脚本改为读本地已落盘的 mp4 文件直接转写。

规则：
  - 输入：knowledge/media/<brand>/video/<text_hash>.mp4（douyin_manifest.json 记录的 ok 视频）
  - 跳过：media_text 表已有 asr 的 content_id
  - 幂等：结果写 media_text（content_id, kind='asr', source='video', text）
  - 纯音乐/BGM 无有效语音 → 跳过不写
  - 节奏：CPU 转写本身较慢，不额外限流

用法：
  python crawler/asr_douyin_local.py --report        # 只看待转写量
  python crawler/asr_douyin_local.py                 # 全量补做
  python crawler/asr_douyin_local.py --limit 5       # 先试 5 条
  python crawler/asr_douyin_local.py --brand MitoQ   # 只做某品牌
"""
import argparse
import json
import os
import sqlite3
import sys
import re
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "db", "cosmobounty.db")
MEDIA_ROOT = os.path.join(PROJ, "knowledge", "media")
MANIFEST = os.path.join(MEDIA_ROOT, "douyin_manifest.json")
MODEL_DIR = os.path.join(PROJ, "tools", "whisper-models", "faster-whisper-small")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_manifest():
    if not os.path.exists(MANIFEST):
        return {}
    return json.load(open(MANIFEST, encoding="utf-8"))


# whisper 常见幻听尾句：无口播时模型会自己编字幕尾巴，必须剔除
_HALLU = (
    "请不吝点赞", "订阅", "转发", "打赏", "字幕由", "amara", "字幕志愿者",
    "谢谢观看", "感谢观看", "明镜与点点", "点栏目", "我们下期再见",
)


def _clean(text):
    """去掉空白与 whisper 幻听尾巴，返回可用正文。"""
    if not text:
        return ""
    t = re.sub(r"\s+", "", text)
    for h in _HALLU:
        t = t.replace(h, "")
    # 纯标点/纯符号视为无效
    if len(re.sub(r"[，。！？、,.!?~～…\-—_\"'（）()【】\[\]：:；;]", "", t)) < 6:
        return ""
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只统计待转写量")
    ap.add_argument("--limit", type=int, default=0, help="最多转写 N 条")
    ap.add_argument("--brand", default="", help="只做某品牌")
    ap.add_argument("--model", default="small", help="whisper 模型")
    ap.add_argument("--only-thin", action="store_true",
                    help="只补正文空且无媒体文本的条目（原始表里显示「仅标题」的）")
    a = ap.parse_args()

    # 已下载成功的视频：content_id -> 本地路径
    # 优先扫磁盘（backfill_douyin_video.py 新下的不会写 manifest），
    # 再并上 manifest 里的记录，避免漏掉任一边。
    local = {}
    import glob as _glob
    by_aid = {}
    for p in _glob.glob(os.path.join(MEDIA_ROOT, "*", "video", "douyin:*.mp4")):
        if os.path.getsize(p) < 4096:
            continue
        by_aid[os.path.basename(p)[len("douyin:"):-4]] = p
    m = load_manifest()
    for it in m.get("items", []):
        if it.get("kind") == "video" and it.get("status") == "ok":
            p = os.path.join(PROJ, it["path"])
            if os.path.exists(p) and os.path.getsize(p) > 4096:
                local[it["content_id"]] = p

    con = sqlite3.connect(DB)
    # 用 aweme_id 把磁盘文件对回 content_id（比 manifest 可靠）
    for cid, url in con.execute(
            "SELECT id, url FROM content WHERE platform='douyin'"):
        aid = (url or "").rstrip("/").split("/")[-1]
        if aid in by_aid:
            local[cid] = by_aid[aid]
    # 已做 ASR 的 content_id
    asr_done = {r[0] for r in con.execute(
        "SELECT DISTINCT content_id FROM media_text WHERE kind='asr'")}

    cond = "c.platform='douyin'"
    params = []
    if a.brand:
        cond += " AND c.brand=?"
        params.append(a.brand)
    if a.only_thin:
        # 只补「正文空 + 无任何媒体文本」的条目：这些在原始数据表里只能显示
        # 「仅标题」，是最该补的。全量 ASR 命中率很低（多数短视频只有 BGM），
        # 精准补才是有效动作。
        cond += (" AND LENGTH(COALESCE(c.body,''))<30 AND NOT EXISTS ("
                 "SELECT 1 FROM media_text mt WHERE mt.content_id=c.id)")
    rows = con.execute(
        f"SELECT c.id, c.brand, c.title FROM content c WHERE {cond} ORDER BY c.id",
        params).fetchall()

    todo = [(cid, brand, title) for cid, brand, title in rows
            if cid in local and cid not in asr_done]

    print(f"抖音 content 共 {len(rows)} 条，已下载视频 {len(local)} 条，"
          f"已有 ASR {len(asr_done & set(local))} 条，待转写 {len(todo)} 条")

    if a.report:
        con.close()
        return

    if a.limit:
        todo = todo[:a.limit]

    # 加载 whisper 模型（CPU int8）
    from faster_whisper import WhisperModel
    src = MODEL_DIR if os.path.exists(os.path.join(MODEL_DIR, "model.bin")) else a.model
    print(f"加载 whisper-{a.model}（{'本地' if src == MODEL_DIR else '远程'}）…", flush=True)
    model = WhisperModel(src, device="cpu", compute_type="int8")

    done = fail = empty = 0
    for cid, brand, title in todo:
        path = local[cid]
        try:
            # 第一轮：开 VAD（阈值放宽，短视频容易被整段误判为静音）
            segments, _ = model.transcribe(
                path, language="zh", vad_filter=True,
                vad_parameters=dict(threshold=0.35, min_speech_duration_ms=200,
                                    min_silence_duration_ms=400, speech_pad_ms=200),
                condition_on_previous_text=False)
            text = _clean("".join(s.text.strip() for s in segments))
            # 第二轮：VAD 没捞到东西时，关掉 VAD 再试，按 no_speech_prob 过滤
            if len(text) < 8:
                segs2, _ = model.transcribe(
                    path, language="zh", vad_filter=False,
                    condition_on_previous_text=False, no_speech_threshold=0.85)
                t2 = _clean("".join(s.text.strip() for s in segs2
                                    if getattr(s, "no_speech_prob", 0) < 0.65))
                if len(t2) > len(text):
                    text = t2
            if not text:
                empty += 1
                print(f"[asr] id={cid} {title[:22]}… 无有效语音（BGM/纯音乐）", flush=True)
                continue
            con.execute(
                "INSERT OR REPLACE INTO media_text (content_id,kind,source,text,created_at)"
                " VALUES (?,?,?,?,?)", (cid, "asr", "video", text, now()))
            con.commit()
            done += 1
            print(f"[asr] id={cid} {title[:22]}… {len(text)} 字", flush=True)
        except Exception as e:
            fail += 1
            print(f"[asr失败] id={cid}: {repr(e)[:100]}", file=sys.stderr)

    con.close()
    print(f"\nASR 完成：成功 {done}，无语音 {empty}，失败 {fail}")


if __name__ == "__main__":
    main()
