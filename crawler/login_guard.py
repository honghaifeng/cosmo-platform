#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
登录守卫：抓取前先确保小红书已登录，登录态写进 profile 后所有批次共用。

为什么单独做：creator 模式每个批次都会新开浏览器，之前每批都在等扫码，
没人扫就白等 10 分钟、最后整轮 0 条。先登录一次，后面全部复用。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
UV = os.path.expanduser("~/.local/bin/uv")

WAIT_MIN = int(os.environ.get("CB_LOGIN_WAIT_MIN", "25"))


def main():
    prof = os.path.join(MC, "browser_data", "xhs_user_data_dir")
    # 清掉占着 profile 的残留浏览器
    subprocess.run(["pkill", "-f", f"user-data-dir={prof}"], capture_output=True)
    time.sleep(2)
    for f in os.listdir(prof):
        if f.startswith("Singleton"):
            try:
                os.remove(os.path.join(prof, f))
            except OSError:
                pass

    env = dict(os.environ)
    env["CB_KEYWORDS"] = "MitoQ"      # 只为触发一次登录检查
    env["CB_MAX_NOTES"] = "1"
    env["CB_COMMENTS"] = "0"
    env["CB_IDENTITY"] = "1"

    print("=" * 56, flush=True)
    print("请在弹出的 Chrome 窗口里扫码登录小红书", flush=True)
    print(f"最多等待 {WAIT_MIN} 分钟；登录态会存进 profile，后续所有批次自动复用", flush=True)
    print("=" * 56, flush=True)

    log = os.path.join(ROOT, "logs", "login_guard.log")
    with open(log, "w", encoding="utf-8") as fh:
        p = subprocess.run([UV, "run", "python", "main.py", "--platform", "xhs",
                            "--lt", "qrcode", "--type", "search"],
                           cwd=MC, env=env, stdout=fh, stderr=subprocess.STDOUT,
                           timeout=WAIT_MIN * 60 + 120)
    txt = open(log, encoding="utf-8", errors="ignore").read()
    ok = "Login status confirmed" in txt
    print("登录结果:", "成功 ✅" if ok else "未完成（超时）", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
