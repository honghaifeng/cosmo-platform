#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打开可见的浏览器窗口供人工扫码登录，并保持窗口存活。

用法：
  tools/MediaCrawler/.venv/bin/python scripts/login_window.py xhs     # 小红书
  tools/MediaCrawler/.venv/bin/python scripts/login_window.py dy      # 抖音

窗口会打开对应平台首页，登录成功后登录态写入该平台的 user_data_dir，
之后 MediaCrawler 采集可复用，无需再次扫码。
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = {
    "xhs": (os.path.join(ROOT, "tools/MediaCrawler/browser_data/xhs_user_data_dir"),
            "https://www.xiaohongshu.com/explore", "/tmp/xhs_now.png"),
    "dy": (os.path.join(ROOT, "tools/MediaCrawler/browser_data/dy_user_data_dir"),
           "https://www.douyin.com/", "/tmp/dy_now.png"),
}


async def main(platform: str):
    from playwright.async_api import async_playwright

    profile, url, shot = PROFILES[platform]
    os.makedirs(profile, exist_ok=True)
    # 清理上次异常退出留下的 Singleton 锁，否则会报「正在现有的浏览器会话中打开」
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(profile, name)
        if os.path.islink(p) or os.path.exists(p):
            try:
                os.unlink(p)
                print(f"已清理陈旧锁: {name}", flush=True)
            except OSError as e:
                print(f"清理锁失败 {name}: {e}", flush=True)
    async with async_playwright() as p:
        # 优先用本机正版 Chrome 内核（Playwright 自带 Chromium 在本机渲染进程会崩）
        launch_kw = dict(
            user_data_dir=profile,
            headless=False,
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-gpu", "--disable-gpu-sandbox",
                  "--disable-dev-shm-usage"],
        )
        try:
            ctx = await p.chromium.launch_persistent_context(channel="chrome", **launch_kw)
            print("内核: 本机 Google Chrome", flush=True)
        except Exception as e:
            print(f"Chrome 通道不可用（{e}），回退 Playwright Chromium", flush=True)
            ctx = await p.chromium.launch_persistent_context(**launch_kw)

        async def open_page():
            pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(4000)
            print("已打开:", pg.url, flush=True)
            print("窗口标题:", await pg.title(), flush=True)
            try:
                await pg.screenshot(path=shot)
                print("截图:", shot, flush=True)
            except Exception as e:
                print("截图失败:", e, flush=True)
            return pg

        page = await open_page()
        last = ""
        crashes = 0
        for i in range(360):          # 最多保持 30 分钟
            try:
                await page.wait_for_timeout(5000)
                cur = page.url
            except Exception as e:
                # 渲染进程崩溃：重建页面继续等待（登录态写在 profile 里，不丢）
                crashes += 1
                print(f"[{i*5}s] 页面异常（{str(e).splitlines()[0][:80]}），第 {crashes} 次重建", flush=True)
                if crashes > 5:
                    print("连续崩溃过多，退出", flush=True)
                    break
                try:
                    for pg in list(ctx.pages):
                        try:
                            await pg.close()
                        except Exception:
                            pass
                    page = await open_page()
                    last = ""
                    continue
                except Exception as e2:
                    print("重建失败:", e2, flush=True)
                    break
            if cur != last:
                print(f"[{i * 5}s] {cur[:80]}", flush=True)
                last = cur
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "xhs"))
