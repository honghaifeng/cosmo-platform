#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""媒体文字提取通用工具：OCR（图片文字）+ ASR（视频口播转写）

设计原则：
  - 正文（content.body）永远是平台原文，本工具绝不改写 body
  - 提取结果写入独立表 media_text(content_id, kind, source, text)
    kind: ocr=图片文字提取；asr=视频口播转写
    source: image:0 / image:1 ... / video

用法（用 managed venv 跑）：
  PY=/Users/v/.workbuddy/binaries/python/envs/default/bin/python
  $PY crawler/extract_media_text.py ocr  --platform xhs     --short-body-only --limit 50
  $PY crawler/extract_media_text.py asr  --platform douyin  --top 30
  $PY crawler/extract_media_text.py ocr  --all              # 全库增量（每日流水线用）
  $PY crawler/extract_media_text.py asr  --all --top 20

依赖（envs/default 已装）：rapidocr-onnxruntime、faster-whisper、requests
"""
import argparse
import json
import os
import sqlite3
import sys
import datetime
import tempfile

# 本工具直连国内 CDN，绕过（可能失效的）系统代理；whisper 模型走国内镜像
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # 镜像不支持 Xet 协议

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def http():
    """不读环境代理的 requests 会话"""
    import requests
    s = requests.Session()
    s.trust_env = False
    return s


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pick_rows(con, platform, kind, args):
    """选出待处理的内容行：有对应媒体且 media_text 还没提过的。"""
    cond, params = [], []
    if platform and platform != "all":
        cond.append("c.platform=?")
        params.append(platform)
    if args.brand:
        cond.append("c.brand=?")
        params.append(args.brand)
    if kind == "ocr":
        cond.append("c.media_json LIKE '%images%'")
    else:
        cond.append("c.media_json LIKE '%video%'")
    if getattr(args, "fresh_days", 0):
        # xhs/douyin 的媒体直链带签名，通常数小时内失效 → 只处理近期入库的
        cond.append("c.collect_time >= datetime('now','localtime', ?)")
        params.append(f"-{args.fresh_days} day")
    if getattr(args, "short_body_only", False):
        cond.append("(c.body IS NULL OR LENGTH(TRIM(c.body))<60)")
    cond.append("NOT EXISTS (SELECT 1 FROM media_text m WHERE m.content_id=c.id AND m.kind=?)")
    params.append(kind)
    order = "ORDER BY json_extract(c.metrics_json,'$.likes') DESC" if kind == "asr" else "ORDER BY c.id"
    sql = f"""SELECT c.id, c.platform, c.title, c.media_json FROM content c
              WHERE {' AND '.join(cond)} {order}"""
    return con.execute(sql, params).fetchall()


# ---------------------------------------------------------------- OCR
def run_ocr(rows):
    from rapidocr_onnxruntime import RapidOCR
    import requests
    ocr = RapidOCR()
    con = sqlite3.connect(DB)
    done = empty = fail = expired = hit_total = 0
    for cid, platform, title, mj in rows:
        try:
            images = json.loads(mj or "{}").get("images") or []
        except Exception:
            images = []
        if not images:
            continue
        n_hit = 0
        for i, url in enumerate(images[:12]):  # 每条最多前 12 张，防跑飞
            src = f"image:{i}"
            if con.execute("SELECT 1 FROM media_text WHERE content_id=? AND source=?",
                           (cid, src)).fetchone():
                continue
            try:
                r = http().get(url, headers=UA, timeout=15)
                if r.status_code in (401, 403, 404):
                    # 签名直链过期/防盗链：不是程序错误，重试无意义
                    expired += 1
                    continue
                if r.status_code != 200 or len(r.content) < 500:
                    fail += 1
                    continue
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    f.write(r.content)
                    tmp = f.name
                result, _ = ocr(tmp)
                os.unlink(tmp)
                text = "\n".join(t[1].strip() for t in (result or []) if t[1].strip())
                if text:
                    con.execute("INSERT OR REPLACE INTO media_text (content_id,kind,source,text,created_at)"
                                " VALUES (?,?,?,?,?)", (cid, "ocr", src, text, now()))
                    n_hit += 1
                    hit_total += 1
            except Exception as e:
                fail += 1
                print(f"  [ocr失败] id={cid} img{i}: {repr(e)[:80]}", file=sys.stderr)
        con.commit()
        done += 1
        print(f"[ocr] id={cid} {title[:24]}… 提取 {n_hit}/{len(images)} 张")
    print(f"OCR 完成：处理 {done} 条内容（成功 {hit_total} 张 / 失败 {fail} 次 / 直链过期 {expired} 次）")
    con.close()
    if expired and hit_total == 0:
        print("!! 所有图片直链均已过期（签名 URL 有效期通常 < 6 小时）："
              "OCR 必须在采集后立即执行，不能对历史库存补跑。", file=sys.stderr)
        return 3
    return 0


# ---------------------------------------------------------------- ASR
def run_asr(rows, model_size):
    from faster_whisper import WhisperModel
    import requests
    con = sqlite3.connect(DB)
    model = None
    done = fail = 0
    for cid, platform, title, mj in rows:
        m = json.loads(mj or "{}")
        vurl = m.get("video") or ""
        if not vurl:
            continue
        try:
            if model is None:
                local_dir = os.path.join(BASE, "tools/whisper-models", f"faster-whisper-{model_size}")
                src = local_dir if os.path.exists(os.path.join(local_dir, "model.bin")) else model_size
                print(f"加载 whisper-{model_size}（{'本地模型' if src == local_dir else '首次会自动下载模型'}）…", flush=True)
                model = WhisperModel(src, device="cpu", compute_type="int8")
            r = http().get(vurl, headers={**UA, "Referer": "https://www.douyin.com/"}, timeout=120, stream=True)
            if r.status_code != 200:
                fail += 1
                print(f"  [下载失败] id={cid} HTTP {r.status_code}", file=sys.stderr)
                continue
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                tmp = f.name
            segments, _ = model.transcribe(tmp, language="zh", vad_filter=True)
            text = "".join(s.text.strip() for s in segments)
            os.unlink(tmp)
            if text:
                con.execute("INSERT OR REPLACE INTO media_text (content_id,kind,source,text,created_at)"
                            " VALUES (?,?,?,?,?)", (cid, "asr", "video", text, now()))
                con.commit()
                done += 1
                print(f"[asr] id={cid} {title[:24]}… 转写 {len(text)} 字", flush=True)
            else:
                print(f"[asr] id={cid} 无有效语音（纯音乐/BGM）", flush=True)
        except Exception as e:
            fail += 1
            print(f"  [asr失败] id={cid}: {repr(e)[:100]}", file=sys.stderr)
    print(f"ASR 完成：成功 {done}，失败 {fail}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description="媒体文字提取（OCR/ASR），结果写 media_text 表")
    ap.add_argument("kind", choices=["ocr", "asr"])
    ap.add_argument("--platform", default="all")
    ap.add_argument("--brand", default="")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条内容")
    ap.add_argument("--top", type=int, default=0, help="asr 专用：按点赞数取前 N 条")
    ap.add_argument("--all", action="store_true", help="全库增量（默认只跑缺正文的短条目）")
    ap.add_argument("--fresh-days", type=int, default=0,
                    help="只处理最近 N 天入库的内容（媒体直链会过期，补跑历史无意义）")
    ap.add_argument("--model", default="small", help="whisper 模型：base/small/medium")
    a = ap.parse_args()
    if a.all:
        a.short_body_only = False
    else:
        a.short_body_only = True
    con = sqlite3.connect(DB)
    rows = pick_rows(con, a.platform, a.kind, a)
    con.close()
    cap = a.top if (a.kind == "asr" and a.top) else (a.limit if a.limit else 0)
    if cap:
        rows = rows[:cap]
    print(f"待处理 {len(rows)} 条（kind={a.kind} platform={a.platform}）")
    if a.kind == "ocr":
        return run_ocr(rows)
    run_asr(rows, a.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
