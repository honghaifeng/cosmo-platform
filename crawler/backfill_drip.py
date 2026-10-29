#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""慢补跑器：短批 + 自适应冷却，专门对付小红书的「安全验证」拦截。

为什么需要它：
  逐条开笔记页是唯一能拿到新签名 CDN 地址的路（库里链接 24h 必过期）。
  但连续开 30~40 个页面就会弹「安全验证」，一次性硬跑 500 条必然被打断，
  而且账号状态越跑越差。所以改成**少量多次**：每轮只开十来条，
  一被拦就退避（冷却时间翻倍），冷却完再试；连续两轮零收获才彻底收手。

判据（不猜）：
  · 轮内出现「命中风控」→ 本轮视为被拦，冷却加倍
  · 轮内成功补充文件数 = 0 → 记一次空轮，连续 2 次空轮停
  · 磁盘上目标文件已存在就跳过，所以每轮都是接着上一轮往下做

用法：
  python3 crawler/backfill_drip.py                  # 默认跑 3 小时
  python3 crawler/backfill_drip.py --hours 1 --batch 15
"""
import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
RUNNER = os.path.join(ROOT, "crawler", "backfill_xhs_media.py")


def one_round(batch, sleep):
    """跑一轮，返回 (是否被风控, 成功文件数, 处理笔记数)"""
    cmd = [PY, "-u", RUNNER, "--kind", "both", "--limit", str(batch), "--sleep", str(sleep)]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3600)
    out = (p.stdout or "") + (p.stderr or "")
    blocked = "命中风控" in out
    m = re.search(r"文件成功 (\d+) 失败 (\d+)", out)
    okf = int(m.group(1)) if m else 0
    m2 = re.search(r"笔记成功 (\d+) 失败 (\d+)", out)
    okn = int(m2.group(1)) if m2 else 0
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-2:]
    print(f"    轮结果：笔记 {okn} 条 / 文件 {okf} 个" + ("（被风控）" if blocked else ""), flush=True)
    for ln in tail:
        print(f"      {ln}", flush=True)
    return blocked, okf, okn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--batch", type=int, default=12, help="每轮最多处理几条笔记")
    ap.add_argument("--sleep", type=float, default=5.0, help="轮内每条间隔秒")
    ap.add_argument("--cool", type=float, default=360, help="初始冷却秒")
    a = ap.parse_args()

    t_end = time.time() + a.hours * 3600
    cool = a.cool
    cool_max = 1500
    empty_streak = 0
    rnd = 0
    total_files = total_notes = 0
    print(f"慢补开始：每轮 ≤{a.batch} 条，初始冷却 {cool:.0f}s，最长 {a.hours}h", flush=True)

    while time.time() < t_end:
        rnd += 1
        print(f"\n[{time.strftime('%H:%M:%S')}] 第 {rnd} 轮", flush=True)
        try:
            blocked, okf, okn = one_round(a.batch, a.sleep)
        except subprocess.TimeoutExpired:
            print("    轮超时，冷却后继续", flush=True)
            blocked, okf, okn = True, 0, 0
        total_files += okf
        total_notes += okn

        if okf == 0:
            empty_streak += 1
        else:
            empty_streak = 0
            cool = a.cool          # 有收获就把冷却降回初始值

        if empty_streak >= 3:
            print("\n连续 3 轮零收获，收手（多半不是节奏问题，是这批笔记本身拿不到）", flush=True)
            break
        if blocked:
            cool = min(cool * 1.7, cool_max)

        wait = min(cool, max(0, t_end - time.time()))
        if wait <= 0:
            break
        print(f"    冷却 {wait/60:.1f} 分钟后再来（累计 文件 {total_files} / 笔记 {total_notes}）",
              flush=True)
        time.sleep(wait)

    print(f"\n慢补结束：共 {rnd} 轮，笔记 {total_notes} 条，新增文件 {total_files} 个", flush=True)
    print("下一步：python3 crawler/upload_qiniu.py && python3 crawler/export_workbench_v4.py", flush=True)


if __name__ == "__main__":
    main()
