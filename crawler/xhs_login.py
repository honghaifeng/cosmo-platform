#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书一次性扫码登录（把登录态写回持久化 profile，之后补数据不用再扫）。

背景：
  browser_data/xhs_user_data_dir 里的 web_session 会过期（约 30 天）。
  过期后打开笔记页会被跳 /login，D/E 这类「重开笔记页拿新地址」的补齐就全废。

用法：
  python3 crawler/xhs_login.py            # 弹出窗口，扫码后自动关闭
  python3 crawler/xhs_login.py --check    # 只检查登录态，不弹窗

注意：这是本机可见窗口，需要人扫一次码；登录态保存在 profile 里，之后
backfill_xhs_media.py 复用，不需要反复扫。
"""
import argparse
import datetime
import os
import sqlite3
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "tools", "MediaCrawler", "browser_data", "xhs_user_data_dir")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")


def cookie_state():
    """返回 (web_session 到期时间, 是否仍有效)"""
    p = os.path.join(PROFILE, "Default", "Cookies")
    if not os.path.exists(p):
        return None, False
    tmp = os.path.join(tempfile.mkdtemp(), "ck.db")
    try:
        shutil.copy2(p, tmp)
        c = sqlite3.connect(tmp)
        row = c.execute("select expires_utc from cookies where host_key like '%xiaohongshu%' "
                        "and name='web_session' order by expires_utc desc limit 1").fetchone()
    except Exception:
        return None, False
    if not row:
        return None, False
    exp = datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=row[0] or 0)
    return exp, exp > datetime.datetime.utcnow()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="即使 cookie 未到期也打开窗口")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()

    exp, ok = cookie_state()
    # 注意：cookie 的到期时间不等于「还有效」。实际遇到过 web_session 写着 2027 年到期、
    # 但服务端已作废，打开笔记页照样跳 /login。所以这里只报告，不作为放行依据，
    # 真正判断放在下面「打开页面看有没有登录墙」。
    print(f"当前 web_session 到期：{exp}（{'未到期' if ok else '已过期/缺失'}），"
          f"实际有效性以页面为准", flush=True)
    if a.check:
        sys.exit(0 if ok else 2)

    # 清掉上次异常退出留下的锁，否则 persistent context 打不开
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        f = os.path.join(PROFILE, name)
        if os.path.islink(f) or os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = dict(user_data_dir=PROFILE, headless=False, locale="zh-CN",
                  viewport={"width": 1280, "height": 880}, user_agent=UA,
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**kw)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()

        before = ""
        for ck in ctx.cookies():
            if ck["name"] == "web_session" and "xiaohongshu" in (ck.get("domain") or ""):
                before = ck["value"]

        pg.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(2500)
        body = pg.inner_text("body")[:400]
        need_login = ("登录" in body) or ("扫码" in body)

        if not need_login and not a.force:
            print("页面显示已登录，无需扫码", flush=True)
            ctx.close()
            sys.exit(0)

        # 已登录但被强制重登时，先退出当前登录，方便重新扫
        pg.goto("https://www.xiaohongshu.com/login", wait_until="domcontentloaded", timeout=45000)
        print(">>> 已打开小红书登录窗口，请扫码（二维码在窗口里）。扫完不用管，我会自动检测。", flush=True)
        t0 = time.time()
        changed = False
        while time.time() - t0 < a.timeout:
            pg.wait_for_timeout(3000)
            for ck in ctx.cookies():
                if ck["name"] == "web_session" and "xiaohongshu" in (ck.get("domain") or ""):
                    if ck["value"] and ck["value"] != before:
                        changed = True
                        break
            if changed:
                break
        pg.wait_for_timeout(3000)

        # 扫码后回笔记页实测一次，确认真的能看内容（不是只有 cookie 变了）
        proof = ""
        try:
            pg.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(2500)
            body = pg.inner_text("body")[:400]
            proof = "未登录" if "扫码" in body else "已登录"
        except Exception as e:
            proof = f"验证页打开失败 {type(e).__name__}"

        exp2, ok2 = cookie_state()
        print(f"结果：{'扫码成功' if changed else '超时未检测到扫码'}；页面状态={proof}；"
              f"web_session 到期={exp2}")
        pg.screenshot(path="/tmp/xhs_login_result.png")
        ctx.close()
    sys.exit(0 if changed else 3)


if __name__ == "__main__":
    main()
