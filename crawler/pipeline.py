#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CosmoBounty 数据流水线 v2 —— 标准四段式。

用户定义的架构（2026-10-24）：
  ① 数据抓取   fetch    —— 抓平台原始数据，产出「原始层」JSONL，不做业务判断
  ② 数据清洗+检验 clean  —— 去重、归属判定、字段规范化、逐条质检，产出「清洗层」JSONL
  ③ 数据处理   process  —— 媒体下载（图片/视频）+ 内容解读（OCR/ASR），产出「富化层」
  ④ 进数据库   ingest   —— 统一写入 content / xhs_note / media_text 表

与旧链路的关键差别：
  旧：抓取边写库 → 脏数据直接落库 → 靠闸门事后拦（脏数据留在库里、看不出问题在哪一段）
  新：抓取只落原始层 → 清洗层做所有判断并留下「为什么留/为什么删」的痕迹 →
      富化层补媒体与解读 → 最后才入库
  好处：任何一段出问题都能单独定位重跑，不用从头再来；每段都有可验收的中间产物。

目录约定：
  data/raw/<platform>/<date>.jsonl        原始层（抓取产出，只增不改）
  data/clean/<platform>/<date>.jsonl      清洗层（每条带 accept/reject + reason）
  data/enriched/<platform>/<date>.jsonl   富化层（清洗层 + media/text 解读）
  data/media/<platform>/<id>/...          媒体文件
  db/cosmobounty.db                       数据库

用法：
  # 全量跑（4 段）
  tools/MediaCrawler/.venv/bin/python crawler/pipeline.py --platform baidu --all

  # 只跑某段（可重跑单段，不必从头来）
  tools/MediaCrawler/.venv/bin/python crawler/pipeline.py --platform baidu --stages clean,ingest

  # 查看某段状态与缺口
  tools/MediaCrawler/.venv/bin/python crawler/pipeline.py --platform baidu --status

  # 质检报告（不入库）
  tools/MediaCrawler/.venv/bin/python crawler/pipeline.py --platform baidu --stages clean --dry-run
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "tools", "MediaCrawler", ".venv", "bin", "python")
MANAGED_PY = "/Users/v/.workbuddy/binaries/python/envs/default/bin/python"
DB = os.path.join(ROOT, "db", "cosmobounty.db")
LOG_DIR = os.path.join(ROOT, "logs")
TODAY = datetime.date.today().isoformat()

RAW_DIR = os.path.join(ROOT, "data", "raw")
CLEAN_DIR = os.path.join(ROOT, "data", "clean")
ENRICH_DIR = os.path.join(ROOT, "data", "enriched")
MEDIA_DIR = os.path.join(ROOT, "data", "media")

STAGES = ["fetch", "clean", "process", "ingest"]
STAGE_CN = {"fetch": "① 数据抓取", "clean": "② 数据清洗+检验",
            "process": "③ 数据处理", "ingest": "④ 进数据库"}

# ── 品牌配置 ─────────────────────────────────────────────────────
def load_brands():
    cfg = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))
    return cfg["brands"]


# 品牌真提及正则（与 clean_brand_mentions.py 同口径）
def brand_patterns():
    out = {}
    for b in load_brands():
        words = set()
        for w in [b["name"]] + list(b.get("aliases") or []) + list(b.get("products") or []):
            w = (w or "").strip()
            if len(w) >= 2:
                words.add(w)
        words = {w for w in words if len(w.replace(" ", "")) >= 3 or re.search(r"[一-龥]", w)}
        pat = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        out[b["name"]] = re.compile(pat, re.I) if pat else None
    return out


CATEGORY_HINTS = re.compile(
    r"(抗衰|逆龄|保健|营养|补充剂|胶囊|片剂|补剂|成分|剂量|功效|服用|口碑|测评|种草|"
    r"Healthy|health|Capsule|capsule|supplement|Supplement|姜黄|镁|奶蓟|鱼油|NMN|NAD|槲皮素|"
    r"精华|抗氧|细胞|线粒体|护肝|睡眠|精力)", re.I)
BLOCKLIST = re.compile(
    r"(百度图片|百度健康|百度知道|爱采购|京东商品|铃声|歌词|游戏|战队|环球|时报|优惠券|"
    r"价格对比|批发|说明书|正品代购)")


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, logfile=None, timeout=7200, cwd=ROOT):
    if logfile:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(logfile, "a", encoding="utf-8") as fh:
            p = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                               timeout=timeout)
        return p.returncode
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


# ══════════════════════════════════════════════════════════════
# ① 数据抓取 —— 只负责把原始数据落盘，不做任何业务判断
# ══════════════════════════════════════════════════════════════
def stage_fetch(platform, args):
    """产出：data/raw/<platform>/<date>.jsonl（追加模式，字段标准化）"""
    out_dir = os.path.join(RAW_DIR, platform)
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"{TODAY}.jsonl")
    n_before = count_lines(out_file)

    if platform == "baidu":
        # 复用已验证的浏览器通道采集器
        prof = args.profile_dir or os.path.join(ROOT, "data", "browser_profiles", "baidu")
        rc = subprocess.run(
            [PY, os.path.join(ROOT, "crawler", "baidu_serp_v3.py"),
             "--pages", str(args.pages), "--profile-dir", prof],
            cwd=ROOT, capture_output=True, text=True, timeout=7200).returncode
        # 把 baidu_serp_v3 落盘的 JSON 转成标准原始层
        src_dir = os.path.join(ROOT, "data", "raw", "baidu_v3")
        n = 0
        with open(out_file, "a", encoding="utf-8") as w:
            for f in sorted(glob.glob(os.path.join(src_dir, "*.json"))):
                try:
                    items = json.load(open(f, encoding="utf-8"))
                except Exception:
                    continue
                brand = os.path.splitext(os.path.basename(f))[0]
                for it in items:
                    link = it.get("real_url") or it.get("url") or ""
                    if not link:
                        continue
                    rec = {
                        "uid": hashlib.md5(link.encode()).hexdigest(),
                        "platform": "baidu",
                        "brand": brand,
                        "title": it.get("title") or "",
                        "body": it.get("snippet") or "",
                        "url": link,
                        "publish_time": it.get("publish_time") or "",
                        "keyword": it.get("keyword") or "",
                        "source": "baidu_serp",
                        "collected_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    }
                    w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
        log(f"   抓取完成：新增 {n} 条原始记录 → {os.path.relpath(out_file, ROOT)}")
    else:
        log(f"   ! {platform} 暂无独立抓取器，复用既有 jsonl")
        return 0
    return count_lines(out_file) - n_before


def count_lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


# ══════════════════════════════════════════════════════════════
# ② 数据清洗 + 检验 —— 所有业务判断都在这一段做，留下痕迹
# ══════════════════════════════════════════════════════════════
def stage_clean(platform, args):
    """产出：data/clean/<platform>/<date>.jsonl（每条带 accept/reject + reason）"""
    src = os.path.join(RAW_DIR, platform, f"{TODAY}.jsonl")
    if not os.path.exists(src):
        log(f"   ! 没有原始层文件：{os.path.relpath(src, ROOT)}")
        return 0
    os.makedirs(os.path.join(CLEAN_DIR, platform), exist_ok=True)
    dst = os.path.join(CLEAN_DIR, platform, f"{TODAY}.jsonl")

    pats = brand_patterns()
    seen_urls = set()
    stats = {"总": 0, "重复": 0, "蹭词位": 0, "无标题": 0, "无正文": 0,
             "无时间": 0, "通过": 0}
    accepted, rejected = [], []

    with open(src, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            stats["总"] += 1
            reasons = []

            url = (r.get("url") or "").strip()
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            brand = r.get("brand") or ""
            pt = (r.get("publish_time") or "").strip()

            # 1) 全局 URL 去重（同一落地 URL 只留一条）
            key = url or hashlib.md5((title + brand).encode()).hexdigest()
            if key in seen_urls:
                stats["重复"] += 1
                rejected.append({**r, "accept": 0, "reject_reason": "URL重复"})
                continue
            seen_urls.add(key)

            # 2) 标题/正文非空
            if not title:
                stats["无标题"] += 1
                reasons.append("无标题")
            if len(body) < 30:
                stats["无正文"] += 1
                reasons.append("正文过短")

            # 3) 归属判定（蹭词位拦截）
            p = pats.get(brand)
            text = f"{title} {body}"
            if p and not p.search(text):
                stats["蹭词位"] += 1
                reasons.append("正文/标题未见品牌（蹭词位）")
            elif p and re.fullmatch(r"[A-Za-z0-9+ ]{2,8}", brand or "") \
                    and not CATEGORY_HINTS.search(text):
                stats["蹭词位"] += 1
                reasons.append("英文短品牌词缺品类语境")
            elif BLOCKLIST.search(text):
                stats["蹭词位"] += 1
                reasons.append("命中干扰词（百科/商品/问答页等）")

            # 4) 时间：缺时间不删，标记待补（后续 process 段可以补）
            if not pt:
                stats["无时间"] += 1

            rec = {**r, "accept": 1 if not reasons else 0, "reject_reason": ";".join(reasons)}
            if reasons:
                rejected.append(rec)
            else:
                stats["通过"] += 1
                accepted.append(rec)

    with open(dst, "w", encoding="utf-8") as w:
        for r in accepted + rejected:
            w.write(json.dumps(r, ensure_ascii=False) + "\n")

    log(f"   清洗完成：通过 {stats['通过']} / 总 {stats['总']}")
    for k in ("重复", "蹭词位", "无标题", "无正文", "无时间"):
        if stats[k]:
            log(f"      {k}: {stats[k]}")
    if args.dry_run:
        log("   (dry-run：不写库)")
        return stats["通过"]
    return stats["通过"]


# ══════════════════════════════════════════════════════════════
# ③ 数据处理 —— 媒体下载 + 图片文字/视频口播解读
# ══════════════════════════════════════════════════════════════
def stage_process(platform, args):
    """产出：data/enriched/<platform>/<date>.jsonl（清洗层 + 媒体/解读结果）"""
    src = os.path.join(CLEAN_DIR, platform, f"{TODAY}.jsonl")
    if not os.path.exists(src):
        log(f"   ! 没有清洗层文件：{os.path.relpath(src, ROOT)}")
        return 0
    os.makedirs(os.path.join(ENRICH_DIR, platform), exist_ok=True)
    dst = os.path.join(ENRICH_DIR, platform, f"{TODAY}.jsonl")

    recs = []
    with open(src, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    accepted = [r for r in recs if r.get("accept") == 1]

    # 3a) 媒体下载（小红书有现成下载器；百度正文是文字，无媒体）
    media_dir = os.path.join(MEDIA_DIR, platform)
    os.makedirs(media_dir, exist_ok=True)
    n_media = 0
    if platform == "xhs" and accepted:
        rc = subprocess.run(
            [PY, os.path.join(ROOT, "crawler", "download_xhs_media.py"),
             "--videos", "--images", "--brand-only"],
            cwd=ROOT, capture_output=True, text=True, timeout=5400)
        for line in (rc.stdout or "").splitlines()[-8:]:
            log("      " + line)

    # 3b) 内容解读（OCR / ASR）—— 结果落 media_text 表，ingest 段负责关联
    n_ocr = n_asr = 0
    if platform in ("xhs", "douyin") and accepted:
        for kind, plat in (("ocr", platform), ("asr", platform)):
            cmd = [MANAGED_PY, os.path.join(ROOT, "crawler", "extract_media_text.py"),
                   kind, "--platform", plat, "--short-body-only", "--limit", "40"]
            try:
                r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                   timeout=3600)
                out = r.stdout or ""
                if "OCR" in kind or True:
                    for ln in out.splitlines()[-4:]:
                        log("      " + ln)
            except Exception as e:
                log(f"      {kind} 失败：{type(e).__name__}")

    # 3c) 百度/头条/web：正文与时间需要回原文页补齐（抓取段只拿到 SERP 摘要）
    n_body = n_time = 0
    if platform in ("baidu", "toutiao", "web"):
        sub = os.path.join(ROOT, "crawler", "enrich_baidu_records.py")
        for plat in (["baidu"] if platform == "baidu" else ["toutiao", "web"]):
            try:
                r = subprocess.run([PY, sub, "--platform", plat],
                                   cwd=ROOT, capture_output=True, text=True, timeout=3600)
                out = (r.stdout or "").strip().splitlines()
                for ln in out[-3:]:
                    log("      " + ln)
            except Exception as e:
                log(f"      {plat} 富化失败：{type(e).__name__}")
        # 把富化结果回填到富化层记录
        con = sqlite3.connect(DB)
        for r in accepted:
            row = con.execute(
                "SELECT body, publish_time FROM content WHERE text_hash=?",
                (r.get("uid"),)).fetchone()
            if row:
                if row[0] and len(row[0]) > len(r.get("body") or ""):
                    r["body"] = row[0]
                    n_body += 1
                if row[1] and not r.get("publish_time"):
                    r["publish_time"] = row[1]
                    n_time += 1
        con.close()

    # 3d) 写富化层
    with open(dst, "w", encoding="utf-8") as w:
        for r in accepted:
            r["media_dir"] = media_dir if platform == "xhs" else ""
            r["enriched_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            w.write(json.dumps(r, ensure_ascii=False) + "\n")
    log(f"   处理完成：富化层 {len(accepted)} 条"
        + (f"，补正文 {n_body} 条，补时间 {n_time} 条" if platform != "xhs" else "")
        + f"，媒体目录 {os.path.relpath(media_dir, ROOT)}")
    return len(accepted)


# ══════════════════════════════════════════════════════════════
# ④ 进数据库 —— 唯一写库的地方
# ══════════════════════════════════════════════════════════════
def stage_ingest(platform, args):
    """把富化层写进 content 表（幂等：同 uid 不重复插）"""
    src = os.path.join(ENRICH_DIR, platform, f"{TODAY}.jsonl")
    if not os.path.exists(src):
        log(f"   ! 没有富化层文件：{os.path.relpath(src, ROOT)}")
        return 0
    recs = []
    with open(src, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    if not recs:
        log("   富化层为空")
        return 0

    con = sqlite3.connect(DB)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_ins = n_upd = 0
    for r in recs:
        h = r.get("uid") or hashlib.md5((r.get("url") or r.get("title") or "").encode()).hexdigest()
        exists = con.execute("SELECT id FROM content WHERE text_hash=?", (h,)).fetchone()
        if exists:
            cur = con.execute(
                """UPDATE content SET title=?, body=?, publish_time=COALESCE(NULLIF(?,''), publish_time),
                       url=?, collect_time=? WHERE text_hash=?""",
                (r.get("title") or "", r.get("body") or "", r.get("publish_time") or "",
                 r.get("url") or "", now, h))
            n_upd += cur.rowcount
        else:
            con.execute(
                """INSERT INTO content
                   (brand, platform, content_type, title, body, publish_time,
                    collect_time, url, raw_path, text_hash, tags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (r.get("brand") or "", platform, "article", r.get("title") or "",
                 r.get("body") or "", r.get("publish_time") or "", now,
                 r.get("url") or "", r.get("source") or "", h,
                 r.get("keyword") or ""))
            n_ins += 1
    con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) "
                "VALUES (?,?,?,?,?,?)",
                (platform, "ALL", "pipeline_ingest", now, "ok",
                 f"入库 新增{n_ins} 更新{n_upd}"))
    con.commit()
    con.close()
    log(f"   入库完成：新增 {n_ins} 条，更新 {n_upd} 条")
    return n_ins


# ══════════════════════════════════════════════════════════════
# 状态与体检
# ══════════════════════════════════════════════════════════════
def stage_status(platform):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT platform, COUNT(*) total,
                                  SUM(verified=1) verified,
                                  SUM(publish_time IS NOT NULL AND publish_time!='') has_time,
                                  SUM(LENGTH(COALESCE(body,''))>=220) body_ok
                           FROM content WHERE platform IN ('baidu','toutiao','web','xhs','douyin')
                           GROUP BY platform ORDER BY platform""").fetchall()
    log("=" * 62)
    log("各平台数据状态")
    log("=" * 62)
    log(f"  {'平台':<10}{'总数':>7}{'已校验':>8}{'有时间':>8}{'正文足':>8}{'可展示':>9}")
    for r in rows:
        rate = f"{100*r['verified']/max(r['total'],1):.0f}%"
        log(f"  {r['platform']:<10}{r['total']:>7}{r['verified']:>8}"
            f"{r['has_time']:>8}{r['body_ok']:>8}{rate:>9}")
    log("-" * 62)
    log("各段中间产物：")
    for stage, base in (("原始", RAW_DIR), ("清洗", CLEAN_DIR), ("富化", ENRICH_DIR)):
        for plat in ("baidu", "xhs", "douyin", "toutiao", "web"):
            d = os.path.join(base, plat)
            if os.path.isdir(d):
                fs = glob.glob(os.path.join(d, "*.jsonl"))
                tot = sum(count_lines(f) for f in fs)
                if tot:
                    log(f"  {stage}层 {plat:<10} {len(fs)} 文件 / {tot} 条")
    log("=" * 62)
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="baidu")
    ap.add_argument("--stages", default="", help="逗号分隔：fetch,clean,process,ingest")
    ap.add_argument("--all", action="store_true", help="跑全链路")
    ap.add_argument("--status", action="store_true", help="只输出状态")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--profile-dir", default="")
    args = ap.parse_args()

    if args.status:
        stage_status(args.platform)
        return

    stages = [s.strip() for s in args.stages.split(",") if s.strip()] or STAGES
    for s in stages:
        if s not in STAGES:
            log(f"! 未知阶段：{s}（可选：{','.join(STAGES)}）")
            return

    log(f"流水线 v2 开始  平台={args.platform}  阶段={'>'.join(stages)}")
    t0 = time.time()
    for s in stages:
        log(f"── {STAGE_CN[s]}")
        try:
            if s == "fetch":
                stage_fetch(args.platform, args)
            elif s == "clean":
                stage_clean(args.platform, args)
            elif s == "process":
                stage_process(args.platform, args)
            elif s == "ingest":
                stage_ingest(args.platform, args)
        except Exception as e:
            log(f"   ✗ {s} 异常：{type(e).__name__}: {e}")
            import traceback
            log("   " + traceback.format_exc().replace("\n", "\n   ")[-800:])
    log(f"流水线结束，用时 {time.time()-t0:.0f}s")
    stage_status(args.platform)


if __name__ == "__main__":
    main()
