#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度数据流水线 —— 一条命令跑完全链路。

背景（2026-10-24）：
  之前补百度数据是手工一步步试（抓 SERP → 看缺什么 → 改脚本 → 再抓），
  每一步都要人来判断，token 消耗大且不可复现。
  这次把整条链路固化成脚本：抓 → 富化 → 补时间 → 校验 → 导出 → 体检，
  中间全程打日志，跑完出一份体检报告，缺什么一眼看清。

链路：
  ① 采集   baidu_serp_v3.py        浏览器通道抓 SERP（多关键词 × 多页）
  ② 富化   enrich_baidu_records.py curl 抓原文，抽取正文 + 发布时间
  ③ 兜底   enrich_baidu_browser.py 浏览器通道补 curl 抓不到的站点
  ④ 归属   clean_brand_mentions.py 蹭词位剔除（只报告，不删）
  ⑤ 闸门   verify_and_gate.py      按 H1~H5 打 verified 标记
  ⑥ 守卫   check_data_boundary.py  确认未校验数据流不出去
  ⑦ 体检   xhs_health_check.py     完整度体检
  ⑧ 导出   export_package.py       生成交付包

用法：
  tools/MediaCrawler/.venv/bin/python crawler/baidu_pipeline.py            # 全链路
  tools/MediaCrawler/.venv/bin/python crawler/baidu_pipeline.py --steps 1,2 # 只跑某几步
  tools/MediaCrawler/.venv/bin/python crawler/baidu_pipeline.py --skip-collect
  tools/MediaCrawler/.venv/bin/python crawler/baidu_pipeline.py --report-only
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "tools", "MediaCrawler", ".venv", "bin", "python")
LOG_DIR = os.path.join(ROOT, "logs")
DB = os.path.join(ROOT, "db", "cosmobounty.db")

STEPS = {
    1: ("采集 SERP", "baidu_serp_v3.py", []),
    2: ("curl 富化正文+时间", "enrich_baidu_records.py", []),
    3: ("浏览器兜底富化", "enrich_baidu_browser.py", []),
    4: ("归属清洗检查", "clean_brand_mentions.py", []),
    5: ("校验闸门打标", "verify_and_gate.py", []),
    6: ("数据边界守卫", "check_data_boundary.py", []),
    7: ("完整度体检", "xhs_health_check.py", []),
    8: ("导出交付包", "export_package.py", []),
}


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run(step, extra=None):
    """跑一个步骤，返回 (ok, 输出尾部)。"""
    name, script, base_args = STEPS[step]
    path = os.path.join(ROOT, "crawler", script)
    args = [PY, path] + base_args + (extra or [])
    log(f"── 步骤{step} {name} 开始")
    t0 = time.time()
    try:
        p = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=7200)
    except subprocess.TimeoutExpired:
        log(f"   ✗ {name} 超时")
        return False, ""
    dt = time.time() - t0
    out = (p.stdout or "") + (p.stderr or "")
    os.makedirs(LOG_DIR, exist_ok=True)
    lf = os.path.join(LOG_DIR, f"pipeline_step{step}_{script.replace('.py', '')}_"
                               f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    with open(lf, "w", encoding="utf-8") as f:
        f.write(out)
    ok = p.returncode == 0
    tail = "\n".join(out.strip().splitlines()[-12:])
    log(f"   {'✓' if ok else '✗'} {name} 退出码={p.returncode} 用时={dt:.0f}s")
    for line in tail.splitlines():
        print("      " + line, flush=True)
    return ok, out


def snapshot():
    """采集前后的库内状态快照。"""
    import sqlite3
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT platform,
                                 COUNT(*) total,
                                 SUM(verified=1) verified,
                                 SUM(publish_time IS NOT NULL AND publish_time!='') has_time,
                                 SUM(LENGTH(COALESCE(body,''))>=220) body_ok
                          FROM content WHERE platform IN ('baidu','toutiao','web')
                          GROUP BY platform""").fetchall()
    out = {r["platform"]: dict(r) for r in rows}
    con.close()
    return out


def health_report(snap):
    """出一份人可读的缺口体检。"""
    log("\n" + "=" * 64)
    log("百度系数据体检")
    log("=" * 64)
    log(f"  {'平台':<10}{'总数':>7}{'已校验':>8}{'有时间':>8}{'正文足':>8}{'可展示率':>10}")
    for plat, d in sorted(snap.items()):
        rate = f"{100*d['verified']/max(d['total'],1):.1f}%"
        log(f"  {plat:<10}{d['total']:>7}{d['verified']:>8}{d['has_time']:>8}"
            f"{d['body_ok']:>8}{rate:>10}")
    # 未过闸的原因分布
    import sqlite3
    con = sqlite3.connect(DB)
    rows = con.execute("""SELECT substr(COALESCE(verify_note,'未打标'),1,14) reason, COUNT(*) n
                          FROM content WHERE platform IN ('baidu','toutiao','web') AND verified=0
                          GROUP BY reason ORDER BY n DESC""").fetchall()
    con.close()
    if rows:
        log("  未通过校验的原因分布：")
        for reason, n in rows:
            log(f"    {reason:<18}{n:>5}")
    log("=" * 64 + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="", help="只跑指定步骤，如 1,2,5")
    ap.add_argument("--skip-collect", action="store_true", help="跳过第1步采集")
    ap.add_argument("--pages", type=int, default=6, help="每个关键词翻多少页")
    ap.add_argument("--profile-dir", default=os.path.join(ROOT, "data", "browser_profiles", "baidu"))
    ap.add_argument("--report-only", action="store_true", help="只出体检报告，不跑任何步骤")
    args = ap.parse_args()

    if args.report_only:
        health_report(snapshot())
        return

    want = None
    if args.steps:
        want = {int(x) for x in args.steps.split(",") if x.strip()}

    def should(s):
        return want is None or s in want

    t_start = time.time()
    log(f"百度流水线开始  steps={args.steps or '全部'}")
    before = snapshot()
    log("起始状态：" + json.dumps(before, ensure_ascii=False))

    results = {}
    # ① 采集
    if should(1) and not args.skip_collect:
        extra = ["--pages", str(args.pages), "--profile-dir", args.profile_dir]
        results[1] = run(1, extra)
    elif should(1):
        log("── 步骤1 跳过（--skip-collect）")

    # ②~③ 富化（curl 优先，浏览器兜底补剩下的）
    if should(2):
        results[2] = run(2, ["--platform", "baidu"])
    if should(3):
        bt_profile = os.path.join(ROOT, "data", "browser_profiles", "baidu_bt")
        results[3] = run(3, ["--platform", "baidu", "--profile-dir", bt_profile])

    # ④ 归属检查（只报告）
    if should(4):
        results[4] = run(4)

    # ⑤ 闸门
    if should(5):
        results[5] = run(5)

    # ⑥⑦ 守卫 + 体检
    if should(6):
        results[6] = run(6)
    if should(7):
        results[7] = run(7)

    # ⑧ 导出
    if should(8):
        results[8] = run(8)

    after = snapshot()
    health_report(after)

    ok_n = sum(1 for v in results.values() if v[0])
    log(f"步骤完成：成功 {ok_n}/{len(results)}，总用时 {time.time()-t_start:.0f}s")
    # 结果摘要落库，供外部查看
    with open(os.path.join(LOG_DIR, f"pipeline_{datetime.date.today()}.json"),
              "w", encoding="utf-8") as f:
        json.dump({"before": before, "after": after,
                   "steps": {k: v[0] for k, v in results.items()}}, f,
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
